# src/copalpm/templates.py
"""Project template storage, validation, migration, and application.

A template is a YAML file at ``<DATA_DIR>/templates/<NN>-<id>.yaml`` declaring:

  schema_version: 1
  id:     <slug, lowercase ascii>
  name:   <display name>
  folders: [<safe relative path>, ...]
  fields: [{key, label, kind, default, options?}, ...]

Field kinds (v1): text, select, list, bool.

The public surface — load_all / load_by_id / save_template / delete_template /
import_template / export_template / validate_template / apply_to_record /
_validate_template_folder_path — is consumed by the CLI handlers in
``template_cli`` and the TUI screens in ``tui_app``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from copalpm.config import DATA_DIR, TEMPLATES_FILE
from copalpm.pm import make_slug

# ── Public constants ───────────────────────────────────────────────────────

SCHEMA_VERSION = 1
TEMPLATES_DIR  = DATA_DIR / "templates"

VALID_KINDS = ("text", "select", "list", "bool")

# Reserved field keys — a template field cannot use any of these as `key`.
# These are the structural project.yaml top-level slots populated by
# build_project_record itself (identity, runtime state, nested containers).
RESERVED_FIELD_KEYS = frozenset({
    "id", "slug", "name", "schema_version", "created_at", "deadline",
    "phase_log", "time_entries", "deliverables", "copalvx", "tags", "notes",
    "people", "financial",
})

# Well-known field keys map into nested sub-dicts of the project record.
# Anything not in this table lands at the top level.
UNPACK_TABLE: dict[str, tuple[str, str]] = {
    "client":         ("client",    "name"),
    "client_contact": ("client",    "contact"),
    "director":       ("people",    "director"),
    "producer":       ("people",    "producer"),
    "collaborators":  ("people",    "collaborators"),
    "budget":         ("financial", "quoted_budget"),
    "rate":           ("financial", "rate_per_hour"),
    "est_hours":      ("financial", "estimated_hours"),
}

_FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_TEMPLATE_HEADER = (
    "# CopalPM template — schema v1\n"
    "# Reference: copalpm/CLAUDE.md  (Templates)\n\n"
)

# Transient Windows errors during os.replace — same set as save_yaml.
_WIN_TRANSIENT_REPLACE_ERRORS = {5, 32}


# ── Defaults (fresh install) ───────────────────────────────────────────────

_LEGACY_TYPE_OPTIONS = [
    {"value": "tlc",      "label": "Internal"},
    {"value": "client",   "label": "Client"},
    {"value": "personal", "label": "Personal"},
]

_LEGACY_CATEGORY_OPTIONS = [
    {"value": "tvc",             "label": "TVC"},
    {"value": "digital-signage", "label": "Digital Signage"},
    {"value": "b2b",             "label": "B2B"},
    {"value": "digital",         "label": "Digital"},
]


def _legacy_base_fields(
    type_default: str = "tlc",
    category_default: str = "tvc",
    include_client: bool = True,
    include_director: bool = True,
    include_producer: bool = True,
    include_collaborators: bool = True,
) -> list[dict]:
    """Standard 'legacy 8-field' field set, used by seed and migration."""
    fields: list[dict] = [
        {"key": "type",     "label": "Type",     "kind": "select",
         "default": type_default,     "options": list(_LEGACY_TYPE_OPTIONS)},
        {"key": "category", "label": "Category", "kind": "select",
         "default": category_default, "options": list(_LEGACY_CATEGORY_OPTIONS)},
    ]
    if include_client:
        fields.append({"key": "client",        "label": "Client",
                       "kind": "text", "default": ""})
    if include_director:
        fields.append({"key": "director",      "label": "Agency / Director",
                       "kind": "text", "default": ""})
    if include_producer:
        fields.append({"key": "producer",      "label": "Producer",
                       "kind": "text", "default": ""})
    if include_collaborators:
        fields.append({"key": "collaborators", "label": "Collaborators",
                       "kind": "list", "default": []})
    return fields


def _builtin_templates() -> list[dict]:
    """Defaults seeded on a clean install (no legacy templates.json)."""
    return [
        {
            "schema_version": SCHEMA_VERSION,
            "id":      "tactical",
            "name":    "Tactical",
            "folders": ["01_Intake", "02_Workfiles", "03_Exports"],
            "fields":  _legacy_base_fields("tlc", "tvc"),
        },
        {
            "schema_version": SCHEMA_VERSION,
            "id":      "digital-signage",
            "name":    "Digital Signage",
            "folders": ["01_Intake", "02_Workfiles", "03_Exports"],
            "fields":  _legacy_base_fields("tlc", "digital-signage",
                                           include_director=False,
                                           include_producer=False),
        },
        {
            "schema_version": SCHEMA_VERSION,
            "id":      "custom",
            "name":    "Custom",
            "folders": ["01_Intake", "02_Workfiles", "03_Exports"],
            "fields":  _legacy_base_fields("tlc", "tvc"),
        },
    ]


# ── Validation ─────────────────────────────────────────────────────────────

def _validate_template_folder_path(path: str) -> str | None:
    """Return an error message if a template folder entry is unsafe, else None.

    Safe = relative, no `..`, no absolute paths, no `~`, no drive letters.

    Both POSIX-style (``a/b/c``) and Windows-style (``a\\b\\c``) accepted.
    Callers should normalize to forward slashes before storage.
    """
    if not isinstance(path, str):
        return "folder path must be a string"
    s = path.strip()
    if not s or s in (".", ".."):
        return f"empty or reserved path: {path!r}"
    if s.startswith(("/", "\\", "~")):
        return f"path must be relative (no leading /, \\, or ~): {path!r}"
    if re.match(r"^[A-Za-z]:", s):
        return f"absolute Windows path not allowed: {path!r}"
    for part in re.split(r"[/\\]+", s):
        if part == "..":
            return f"path traversal not allowed: {path!r}"
        if part == "~":
            return f"home expansion not allowed: {path!r}"
        if not part:
            return f"empty segment in path: {path!r}"
    return None


def _normalize_folder_path(path: str) -> str:
    """Strip whitespace and normalize Windows backslashes to forward slashes."""
    return path.strip().replace("\\", "/")


def validate_template(tmpl: Any) -> list[str]:
    """Return a list of error strings (empty == valid)."""
    errs: list[str] = []
    if not isinstance(tmpl, dict):
        return ["template must be a YAML mapping"]

    if tmpl.get("schema_version") != SCHEMA_VERSION:
        errs.append(
            f"schema_version must be {SCHEMA_VERSION}, got {tmpl.get('schema_version')!r}"
        )

    tid = tmpl.get("id", "")
    if not isinstance(tid, str) or not tid:
        errs.append("id is required")
    elif make_slug(tid) != tid:
        errs.append(
            f"id {tid!r} must be a valid slug (lowercase ascii, dash-separated)"
        )

    name = tmpl.get("name", "")
    if not isinstance(name, str) or not name.strip():
        errs.append("name is required")

    folders = tmpl.get("folders", [])
    if not isinstance(folders, list) or not folders:
        errs.append("folders must be a non-empty list of relative path strings")
    else:
        for i, f in enumerate(folders):
            err = _validate_template_folder_path(f)
            if err:
                errs.append(f"folders[{i}]: {err}")

    fields = tmpl.get("fields", [])
    if not isinstance(fields, list):
        errs.append("fields must be a list (may be empty)")
        return errs

    seen_keys: set[str] = set()
    for i, fd in enumerate(fields):
        if not isinstance(fd, dict):
            errs.append(f"fields[{i}] must be a mapping")
            continue
        key = fd.get("key", "")
        if not isinstance(key, str) or not _FIELD_KEY_RE.match(key):
            errs.append(
                f"fields[{i}].key must match [a-z][a-z0-9_]*, got {key!r}"
            )
        elif key in RESERVED_FIELD_KEYS:
            errs.append(f"fields[{i}].key {key!r} is reserved")
        elif key in seen_keys:
            errs.append(f"fields[{i}].key {key!r} duplicates an earlier field")
        else:
            seen_keys.add(key)

        kind = fd.get("kind", "")
        if kind not in VALID_KINDS:
            errs.append(
                f"fields[{i}].kind {kind!r} must be one of {VALID_KINDS}"
            )

        label = fd.get("label", "")
        if not isinstance(label, str) or not label.strip():
            errs.append(f"fields[{i}].label is required")

        if kind == "select":
            opts = fd.get("options", [])
            if not isinstance(opts, list) or not opts:
                errs.append(
                    f"fields[{i}].options must be a non-empty list for kind=select"
                )
            else:
                values: list = []
                for j, o in enumerate(opts):
                    if (not isinstance(o, dict)
                            or "value" not in o or "label" not in o):
                        errs.append(
                            f"fields[{i}].options[{j}] must have value+label keys"
                        )
                    else:
                        values.append(o["value"])
                # default (if set) must match one of the declared option values.
                # Without this guard, Textual's Select widget raises
                # InvalidSelectValueError at mount time and crashes the InitScreen.
                default_val = fd.get("default")
                if values and default_val is not None and default_val not in values:
                    errs.append(
                        f"fields[{i}].default {default_val!r} must be one of the "
                        f"declared option values: {values}"
                    )
        elif kind == "bool":
            d = fd.get("default")
            if d is not None and not isinstance(d, bool):
                errs.append(
                    f"fields[{i}].default must be a boolean for kind=bool"
                )
        elif kind == "list":
            d = fd.get("default")
            if d is not None and not isinstance(d, list):
                errs.append(
                    f"fields[{i}].default must be a list for kind=list"
                )

    return errs


# ── Application to project records ─────────────────────────────────────────

def template_field_defaults(tmpl: dict) -> dict[str, Any]:
    """Return {key: default_value} for every field declared by a template.

    Used by InitScreen to pre-populate inputs when the user picks a template,
    and as the ``field_values`` argument to ``build_project_record`` when no
    overrides are supplied (eg. when a `select` field's only legal value is
    its declared default).
    """
    out: dict[str, Any] = {}
    for fd in tmpl.get("fields", []):
        if not isinstance(fd, dict):
            continue
        key = fd.get("key")
        if not isinstance(key, str):
            continue
        out[key] = fd.get("default")
    return out


def apply_to_record(field_values: dict[str, Any], record: dict) -> dict:
    """Merge template field values into a project record.

    Well-known keys (``UNPACK_TABLE``) route into the appropriate sub-dict
    (``record.client.name``, ``record.people.director``, etc.). All other
    keys land at the top level of the record.

    Reserved keys (``RESERVED_FIELD_KEYS``) are silently skipped as defense-
    in-depth — ``validate_template`` should already have rejected them.

    Mutates ``record`` in place and returns it for chaining.
    """
    for key, value in field_values.items():
        if key in RESERVED_FIELD_KEYS:
            continue
        if key in UNPACK_TABLE:
            container, sub_key = UNPACK_TABLE[key]
            record.setdefault(container, {})[sub_key] = value
        else:
            record[key] = value
    return record


# ── Atomic file write ─────────────────────────────────────────────────────

def _atomic_replace(tmp: Path, dst: Path) -> None:
    if sys.platform != "win32":
        os.replace(tmp, dst)
        return
    delay = 0.05
    for attempt in range(5):
        try:
            os.replace(tmp, dst)
            return
        except OSError as e:
            if getattr(e, "winerror", None) not in _WIN_TRANSIENT_REPLACE_ERRORS:
                raise
            if attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2


def _write_template_file(path: Path, tmpl: dict) -> None:
    """Atomic write of a template YAML with the canonical header."""
    payload = _TEMPLATE_HEADER + yaml.dump(
        tmpl,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        _atomic_replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ── Discovery / load ──────────────────────────────────────────────────────

def _strip_internal_keys(tmpl: dict) -> dict:
    """Drop keys that start with `_` (in-memory metadata, not for serialization)."""
    return {k: v for k, v in tmpl.items() if not k.startswith("_")}


def _list_template_files() -> list[Path]:
    """Return sorted list of *.yaml files in the templates dir, or []."""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p for p in TEMPLATES_DIR.glob("*.yaml") if p.is_file())


def load_all(_run_migration: bool = True) -> list[dict]:
    """Load all valid templates, sorted by filename.

    Invalid YAML or invalid template structure → skip with a warning to
    stderr. Each returned dict has an internal ``_filename`` key (the
    template's filename on disk).
    """
    if _run_migration:
        _migrate_if_needed()
    out: list[dict] = []
    for path in _list_template_files():
        try:
            tmpl = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as e:
            print(f"warning: skipping malformed template {path.name}: {e}",
                  file=sys.stderr)
            continue
        errors = validate_template(tmpl)
        if errors:
            print(f"warning: skipping invalid template {path.name}: {errors[0]}",
                  file=sys.stderr)
            continue
        # Normalize folder slashes on read (in-memory only)
        tmpl["folders"] = [_normalize_folder_path(f) for f in tmpl["folders"]]
        tmpl["_filename"] = path.name
        out.append(tmpl)
    return out


def load_by_id(template_id: str) -> dict | None:
    for tmpl in load_all():
        if tmpl.get("id") == template_id:
            return tmpl
    return None


# ── Save / delete ─────────────────────────────────────────────────────────

def _next_prefix() -> int:
    """Return the next zero-padded NN prefix for a new template file."""
    max_n = -1
    for path in _list_template_files():
        m = re.match(r"^(\d+)-", path.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def save_template(tmpl: dict, *, allow_id_collision: bool = False) -> Path:
    """Validate and write a template. Returns the saved file path.

    If a template with the same id already exists on disk, the existing
    file is overwritten (preserving its filename prefix). Otherwise a new
    file is created with the next available NN prefix.

    Raises ``ValueError`` on validation failure or (when
    ``allow_id_collision=False``) on id collision via TUI/CLI write paths
    that want to surface "id already in use" to the user. The TemplateScreen
    edit-existing path passes ``allow_id_collision=True``.
    """
    clean = _strip_internal_keys(tmpl)
    # Normalize folder slashes on write
    if "folders" in clean and isinstance(clean["folders"], list):
        clean["folders"] = [
            _normalize_folder_path(f) for f in clean["folders"]
            if isinstance(f, str) and f.strip()
        ]

    errors = validate_template(clean)
    if errors:
        raise ValueError(errors[0])

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    existing_path = _find_file_for_id(clean["id"])
    if existing_path is not None:
        if not allow_id_collision:
            raise ValueError(f"template id {clean['id']!r} already in use")
        target = existing_path
    else:
        target = TEMPLATES_DIR / f"{_next_prefix():02d}-{clean['id']}.yaml"

    _write_template_file(target, clean)
    return target


def _find_file_for_id(template_id: str) -> Path | None:
    """Locate the *.yaml file whose ``id:`` matches, regardless of filename."""
    for path in _list_template_files():
        try:
            t = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            continue
        if isinstance(t, dict) and t.get("id") == template_id:
            return path
    return None


def delete_template(template_id: str) -> bool:
    """Remove a template by id. Returns False if not found."""
    path = _find_file_for_id(template_id)
    if path is None:
        return False
    try:
        path.unlink()
    except OSError as e:
        print(f"warning: could not delete {path}: {e}", file=sys.stderr)
        return False
    return True


# ── Import / export ───────────────────────────────────────────────────────

def import_template(src: Path, *, force: bool = False) -> dict:
    """Validate and copy a template YAML into the templates dir.

    Raises ``FileNotFoundError`` / ``ValueError`` on missing file, malformed
    YAML, or invalid structure. On id collision, raises ``ValueError`` unless
    ``force=True`` (which overwrites the existing entry). Returns the loaded
    template dict (re-read from its destination).
    """
    if not src.exists():
        raise FileNotFoundError(f"template file not found: {src}")
    try:
        tmpl = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"malformed YAML: {e}") from e

    errors = validate_template(tmpl)
    if errors:
        raise ValueError(f"invalid template: {errors[0]}")

    existing = load_by_id(tmpl["id"])
    if existing and not force:
        raise ValueError(
            f"template id {tmpl['id']!r} already exists; pass force=True to overwrite"
        )

    save_template(tmpl, allow_id_collision=True)
    loaded = load_by_id(tmpl["id"])
    if loaded is None:
        raise RuntimeError(
            f"internal error: just-imported template {tmpl['id']!r} not found"
        )
    return loaded


def export_template(template_id: str, dst: Path) -> Path:
    """Copy a template's YAML to ``dst`` (or ``dst/<id>.yaml`` if dst is a dir)."""
    tmpl = load_by_id(template_id)
    if tmpl is None:
        raise ValueError(f"no template with id {template_id!r}")
    src = TEMPLATES_DIR / tmpl["_filename"]
    target = dst / f"{template_id}.yaml" if dst.is_dir() else dst
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return target


# ── Migration ──────────────────────────────────────────────────────────────

def _legacy_to_v1(entry: dict) -> dict:
    """Convert a legacy templates.json entry to a v1 template dict.

    Field generation rule: ``type`` and ``category`` always emitted (with the
    legacy hardcoded option lists). ``client``/``director``/``producer``/
    ``collaborators`` emitted only if the legacy value is not ``None`` — a
    legacy ``None`` means "the user cleared this slot for this template"
    and we honor it by omitting the field.
    """
    name = entry.get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("legacy entry missing name")
    slug = make_slug(name)
    if not slug:
        raise ValueError(f"could not derive slug from name {name!r}")

    fields: list[dict] = [
        {"key": "type", "label": "Type", "kind": "select",
         "default": entry.get("type") or "tlc",
         "options": list(_LEGACY_TYPE_OPTIONS)},
        {"key": "category", "label": "Category", "kind": "select",
         "default": entry.get("category") or "tvc",
         "options": list(_LEGACY_CATEGORY_OPTIONS)},
    ]
    if entry.get("client") is not None:
        fields.append({"key": "client", "label": "Client",
                       "kind": "text", "default": entry.get("client") or ""})
    if entry.get("director") is not None:
        fields.append({"key": "director", "label": "Agency / Director",
                       "kind": "text", "default": entry.get("director") or ""})
    if entry.get("producer") is not None:
        fields.append({"key": "producer", "label": "Producer",
                       "kind": "text", "default": entry.get("producer") or ""})
    if "collaborators" in entry:
        # The new `kind: list` only stores list[str]. Some legacy templates
        # stored richer structures like {"name": "X", "role": "audio"} — flatten
        # them to readable strings on migration so users don't lose context.
        raw = entry.get("collaborators") or []
        flat: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    flat.append(item)
                elif isinstance(item, dict) and "name" in item:
                    name = str(item["name"])
                    role = item.get("role")
                    flat.append(f"{name} ({role})" if role else name)
                else:
                    flat.append(str(item))
        fields.append({"key": "collaborators", "label": "Collaborators",
                       "kind": "list", "default": flat})

    folders = entry.get("folders") or ["01_Intake", "02_Workfiles", "03_Exports"]

    return {
        "schema_version": SCHEMA_VERSION,
        "id":      slug,
        "name":    name,
        "folders": folders,
        "fields":  fields,
    }


def _unique_slug(slug: str, used: set[str]) -> str:
    if slug not in used:
        return slug
    n = 2
    while f"{slug}-{n}" in used:
        n += 1
    return f"{slug}-{n}"


def _migrate_legacy() -> None:
    """One-shot: read legacy templates.json, emit one YAML per entry, .bak the source."""
    try:
        legacy = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"warning: could not read legacy {TEMPLATES_FILE}: {e}",
              file=sys.stderr)
        return
    if not isinstance(legacy, list):
        print(f"warning: legacy {TEMPLATES_FILE} is not a list; skipping migration",
              file=sys.stderr)
        return

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    used_slugs: set[str] = set()
    written = 0
    for i, entry in enumerate(legacy):
        try:
            tmpl = _legacy_to_v1(entry)
        except Exception as e:
            print(f"warning: skipping legacy template #{i}: {e}",
                  file=sys.stderr)
            continue
        slug = _unique_slug(tmpl["id"], used_slugs)
        tmpl["id"] = slug
        used_slugs.add(slug)
        filename = f"{i:02d}-{slug}.yaml"
        try:
            _write_template_file(TEMPLATES_DIR / filename, tmpl)
            written += 1
        except OSError as e:
            print(f"warning: could not write {filename}: {e}",
                  file=sys.stderr)
            continue

    # Rename legacy file to .bak even if some entries failed — the user can
    # rescue them by hand from the backup. Don't risk re-migrating on next launch.
    if written > 0 or True:
        timestamp = datetime.now().strftime("%Y-%m-%d")
        backup_path = TEMPLATES_FILE.with_name(
            f"templates.json.migrated-{timestamp}.bak"
        )
        try:
            os.replace(TEMPLATES_FILE, backup_path)
        except OSError as e:
            print(f"warning: could not rename legacy file to {backup_path.name}: {e}",
                  file=sys.stderr)


def _seed_defaults() -> None:
    """Write the built-in templates on a clean install."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    for i, tmpl in enumerate(_builtin_templates()):
        filename = f"{i:02d}-{tmpl['id']}.yaml"
        try:
            _write_template_file(TEMPLATES_DIR / filename, dict(tmpl))
        except OSError as e:
            print(f"warning: could not seed {filename}: {e}", file=sys.stderr)


def _migrate_if_needed() -> None:
    """Idempotent: migrate legacy templates.json, or seed defaults, or no-op.

    Logic:
    - If TEMPLATES_DIR has at least one .yaml file → no-op (already done).
    - Else if templates.json exists → migrate it, rename to .bak.
    - Else → seed built-in defaults.

    If after migration both ``templates/`` and ``templates.json`` exist
    (e.g. user restored a backup), warn once and ignore the legacy file.
    """
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    if _list_template_files():
        if TEMPLATES_FILE.exists():
            print(
                f"warning: legacy {TEMPLATES_FILE.name} found alongside "
                f"{TEMPLATES_DIR.name}/ — ignoring legacy",
                file=sys.stderr,
            )
        return

    if TEMPLATES_FILE.exists():
        _migrate_legacy()
    else:
        _seed_defaults()
