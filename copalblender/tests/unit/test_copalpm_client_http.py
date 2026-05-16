"""Unit tests for the vendored copalpm_client HTTP wrapper."""

import io
import json
import urllib.error
import urllib.request

import pytest

import copalpm_client  # vendored module on sys.path via conftest.py


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_config(monkeypatch):
    """Make _load_pm_config return a deterministic dict without touching disk."""
    monkeypatch.setattr(copalpm_client, "_load_pm_config", lambda: {"api_key": "test-key", "port": 5123})


class _FakeResponse:
    """Minimal urlopen response stand-in."""
    def __init__(self, body: bytes):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


# ── Happy path ─────────────────────────────────────────────────────────────────

def test_api_success_returns_parsed_json(fake_config, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data
        return _FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = copalpm_client._api("POST", "/start", {"projectId": "PROJ-FOO"})
    assert result == {"ok": True}
    assert captured["url"] == "http://127.0.0.1:5123/start"
    assert captured["method"] == "POST"
    assert captured["headers"]["X-api-key"] == "test-key"
    assert captured["headers"]["Content-type"] == "application/json"
    assert json.loads(captured["body"]) == {"projectId": "PROJ-FOO"}


def test_api_get_sends_no_body(fake_config, monkeypatch):
    def fake_urlopen(req, timeout):
        assert req.data is None
        return _FakeResponse(b'{"ok": true}')
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    copalpm_client._api("GET", "/state")


def test_api_handles_empty_response_body(fake_config, monkeypatch):
    """Some endpoints (none currently) might return 204 / empty body."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _FakeResponse(b""))
    assert copalpm_client._api("GET", "/health") == {}


# ── Error paths ────────────────────────────────────────────────────────────────

def test_api_urlerror_raises_service_down(fake_config, monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(copalpm_client.ServiceDownError):
        copalpm_client._api("GET", "/state")


def test_api_httperror_with_json_body_uses_error_field(fake_config, monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:5123/start",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b'{"error": "unknown_project_id", "projectId": "PROJ-NOPE"}'),
        )
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(copalpm_client.ApiError) as ei:
        copalpm_client._api("POST", "/start", {"projectId": "PROJ-NOPE"})
    assert ei.value.code == 404
    assert ei.value.message == "unknown_project_id"


def test_api_httperror_with_hint_falls_back_to_hint(fake_config, monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:5123/start",
            code=409,
            msg="Conflict",
            hdrs={},
            fp=io.BytesIO(b'{"hint": "no registry"}'),
        )
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(copalpm_client.ApiError) as ei:
        copalpm_client._api("POST", "/start", {"projectId": "PROJ-NOPE"})
    assert ei.value.message == "no registry"


def test_api_httperror_with_non_json_body_uses_raw_text(fake_config, monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:5123/start",
            code=500,
            msg="Server Error",
            hdrs={},
            fp=io.BytesIO(b"plain text error"),
        )
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(copalpm_client.ApiError) as ei:
        copalpm_client._api("POST", "/start", {"projectId": "PROJ-NOPE"})
    assert ei.value.message == "plain text error"


# ── _load_pm_config ────────────────────────────────────────────────────────────

def test_load_pm_config_missing_raises_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(copalpm_client, "_copalpm_config_path", lambda: tmp_path / "missing.json")
    with pytest.raises(copalpm_client.NotInstalledError):
        copalpm_client._load_pm_config()


def test_load_pm_config_reads_json(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"api_key": "abc", "port": 9999}), encoding="utf-8")
    monkeypatch.setattr(copalpm_client, "_copalpm_config_path", lambda: cfg)
    assert copalpm_client._load_pm_config() == {"api_key": "abc", "port": 9999}


# ── Wrappers ───────────────────────────────────────────────────────────────────

def test_start_posts_expected_body(fake_config, monkeypatch):
    captured = {}
    def fake_api(method, endpoint, body=None, **kw):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"ok": True, "session_id": "S-x"}
    monkeypatch.setattr(copalpm_client, "_api", fake_api)
    copalpm_client.start("PROJ-FOO", tool="blender", phase=None, description="x")
    assert captured == {
        "method": "POST",
        "endpoint": "/start",
        "body": {"projectId": "PROJ-FOO", "tool": "blender", "phase": None, "description": "x"},
    }


def test_stop_posts_reason(monkeypatch):
    captured = {}
    def fake_api(method, endpoint, body=None, **kw):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["body"] = body
        return {"ok": True}
    monkeypatch.setattr(copalpm_client, "_api", fake_api)
    copalpm_client.stop(reason="inactivity")
    assert captured == {"method": "POST", "endpoint": "/stop", "body": {"reason": "inactivity"}}


def test_ping_posts_no_body(monkeypatch):
    captured = {}
    def fake_api(method, endpoint, body=None, **kw):
        captured["body"] = body
        captured["endpoint"] = endpoint
        return {"ok": True, "active": True}
    monkeypatch.setattr(copalpm_client, "_api", fake_api)
    copalpm_client.ping()
    assert captured["endpoint"] == "/ping"
    assert captured["body"] is None


def test_state_returns_none_for_empty(monkeypatch):
    monkeypatch.setattr(copalpm_client, "_api", lambda *a, **kw: {})
    assert copalpm_client.state() is None


def test_state_returns_session_dict(monkeypatch):
    monkeypatch.setattr(copalpm_client, "_api", lambda *a, **kw: {"session_id": "S-1", "project_id": "PROJ-X"})
    assert copalpm_client.state() == {"session_id": "S-1", "project_id": "PROJ-X"}
