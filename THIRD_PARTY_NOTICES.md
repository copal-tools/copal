# Third-Party Notices

Copal Tools (CopalVX + CopalPM) is licensed under **Apache License 2.0**.
This file lists the third-party dependencies the project relies on, their
upstream licenses, and notes on compatibility with Apache 2.0.

Last verified: 2026-05-14.

## Direct Python dependencies

### CopalVX client (`copalvx/client/pyproject.toml`)

| Package | Upstream license | Compatible with Apache 2.0 | Notes |
|---|---|---|---|
| [requests](https://pypi.org/project/requests/) | Apache-2.0 | Yes | — |
| [pathspec](https://pypi.org/project/pathspec/) | MPL-2.0 | Yes | Mozilla Public License has file-level weak copyleft. Consumed as an unmodified library — no triggering condition. |

### CopalVX server (`copalvx/server/app/pyproject.toml`)

| Package | Upstream license | Compatible with Apache 2.0 | Notes |
|---|---|---|---|
| [fastapi](https://pypi.org/project/fastapi/) | MIT | Yes | — |
| [uvicorn](https://pypi.org/project/uvicorn/) | BSD-3-Clause | Yes | — |
| [sqlalchemy](https://pypi.org/project/SQLAlchemy/) | MIT | Yes | — |
| [psycopg2-binary](https://pypi.org/project/psycopg2-binary/) | LGPL-3.0 + OpenSSL exception | Yes | LGPL component is dynamically linked via Python import; users can swap the wheel without touching Copal Tools source. |
| [requests](https://pypi.org/project/requests/) | Apache-2.0 | Yes | — |

### CopalPM (`copalpm/pyproject.toml`)

| Package | Upstream license | Compatible with Apache 2.0 | Notes |
|---|---|---|---|
| [flask](https://pypi.org/project/Flask/) | BSD-3-Clause | Yes | — |
| [waitress](https://pypi.org/project/waitress/) | ZPL-2.1 (Zope Public License) | Yes | OSI-approved permissive licence. The "Zope" trademark clause is not exercised by Copal Tools. |
| [pyyaml](https://pypi.org/project/PyYAML/) | MIT | Yes | — |
| [textual](https://pypi.org/project/textual/) | MIT | Yes | — |
| [textual-fspicker](https://pypi.org/project/textual-fspicker/) | MIT | Yes | — |

## Notable transitive dependencies

Sampled at audit time (the full list is reproducible from `uv pip list` in each package's venv):

| Package | Pulled in via | License |
|---|---|---|
| starlette | fastapi | BSD-3-Clause |
| pydantic | fastapi | MIT |
| urllib3 | requests | MIT |
| certifi | requests | MPL-2.0 (library use, same logic as pathspec) |
| idna, charset-normalizer | requests | BSD-3-Clause / MIT |
| jinja2, werkzeug, itsdangerous, click, markupsafe, blinker | flask | BSD-3-Clause / MIT |
| rich, markdown-it-py, mdurl | textual | MIT |
| platformdirs | textual | MIT |

## Runtime infrastructure (Docker images, server only)

Pulled at runtime, not redistributed by Copal Tools:

| Image | Upstream license | Notes |
|---|---|---|
| `ghcr.io/chrislusf/seaweedfs:latest` | Apache-2.0 | — |
| `postgres:15-alpine` | PostgreSQL Licence (permissive, BSD-style) | — |
| `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` | Apache-2.0 / MIT (dual) | Build-time only inside the API container. |

## Vendored code

**None.** Every third-party dependency is declared in `pyproject.toml` and
fetched at install time by `uv`. No third-party source files are copied
into this repository.

## Compatibility summary

- **No GPL, AGPL, SSPL, BSL, or "source-available" licenses** anywhere in
  the dependency tree.
- All copyleft licences present (MPL-2.0, LGPL-3.0) operate at a
  granularity (file-level for MPL, separately distributable library for
  LGPL) that does not require Copal Tools to re-license.
- The Apache 2.0 NOTICE-file inclusion requirement (§4(d) of the licence)
  is satisfied: no consumed Apache-2.0 dependency ships a non-empty
  NOTICE file that requires re-distribution.

## How to reproduce this audit

```powershell
# CopalVX client
Set-Location E:\Development\copal\copalvx\client
uv sync
uv run python -c "import importlib.metadata as m; [print(p, m.metadata(p).get('License-Expression') or m.metadata(p).get('License')) for p in ['requests','pathspec']]"

# CopalPM
Set-Location E:\Development\copal\copalpm
uv sync
uv run python -c "import importlib.metadata as m; [print(p, m.metadata(p).get('License-Expression') or m.metadata(p).get('License')) for p in ['flask','waitress','pyyaml','textual','textual-fspicker']]"
```

If a future dependency bump pulls in a new top-level package, add it to
the table above and re-verify compatibility.
