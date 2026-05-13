"""
Unit tests for Phase Sec-A.4 / Sec-A.5 in the client:
  - delete_project / cleanup_orphans must send the X-Confirm-Delete header
  - identity header sanitization caps length and rejects bad chars
"""

from unittest.mock import MagicMock, patch
from copal_core import api


def _ok():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"status": "ok"}
    m.raise_for_status.return_value = None
    return m


@patch("copal_core.api.requests.delete")
def test_delete_project_sends_confirm_header(mock_delete):
    mock_delete.return_value = _ok()
    api.delete_project("proj", delete_orphan_files=True)
    headers = mock_delete.call_args.kwargs["headers"]
    assert headers["X-Confirm-Delete"] == "yes-permanently"
    # Identity headers also attached so the server-side audit row is accurate
    assert "X-Copal-User" in headers
    assert "X-Copal-Host" in headers


@patch("copal_core.api.requests.post")
def test_cleanup_orphans_sends_confirm_header(mock_post):
    mock_post.return_value = _ok()
    api.cleanup_orphans()
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-Confirm-Delete"] == "yes-permanently"


def test_ident_sanitize_accepts_normal_value():
    assert api._sanitize_ident("stelios", "fallback") == "stelios"
    assert api._sanitize_ident("user.name@host", "fallback") == "user.name@host"
    assert api._sanitize_ident("user-1_2", "fallback") == "user-1_2"


def test_ident_sanitize_falls_back_on_bad_chars():
    # Newline, space, ANSI escape, semicolon — anything outside [\w.@-]
    assert api._sanitize_ident("evil\nuser", "fb") == "fb"
    assert api._sanitize_ident("name with space", "fb") == "fb"
    assert api._sanitize_ident("a;b", "fb") == "fb"


def test_ident_sanitize_truncates_to_64_chars():
    long = "a" * 200
    out = api._sanitize_ident(long, "fb")
    # 200 chars of 'a' matches the regex after the 64-char truncation
    assert out == "a" * 64


def test_ident_sanitize_empty_uses_fallback():
    assert api._sanitize_ident("", "default-author") == "default-author"
    assert api._sanitize_ident(None, "default-author") == "default-author"
    assert api._sanitize_ident("   ", "default-author") == "default-author"
