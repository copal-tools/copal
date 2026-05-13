"""
Unit tests for the X-Copal-User / X-Copal-Host identity headers that the
client must attach to commit and checkout requests. The server now relies
on these for the push/pull activity log.

requests.post / requests.get are mocked — no network or server needed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from copal_core import api


def _mock_response(status_code=200, json_data=None):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data or {}
    m.raise_for_status.return_value = None
    return m


@patch("copal_core.api.socket.gethostname", return_value="LAPTOP")
@patch.dict(api.SETTINGS, {"default_author": "stelios"})
def test_identity_headers_user_and_host(_mock_hostname):
    headers = api._identity_headers()
    assert headers["X-Copal-User"] == "stelios"
    assert headers["X-Copal-Host"] == "LAPTOP"


@patch("copal_core.api.socket.gethostname", return_value="LAPTOP")
@patch.dict(api.SETTINGS, {"default_author": "stelios"})
@patch("copal_core.api.requests.post")
def test_commit_sends_identity_headers(mock_post, _mock_hostname):
    mock_post.return_value = _mock_response()

    api.commit("proj", "v1.0", "msg", "stelios", files=[])

    assert mock_post.called
    sent_headers = mock_post.call_args.kwargs["headers"]
    assert sent_headers["X-Copal-User"] == "stelios"
    assert sent_headers["X-Copal-Host"] == "LAPTOP"


@patch("copal_core.api.socket.gethostname", return_value="LAPTOP")
@patch.dict(api.SETTINGS, {"default_author": "stelios"})
@patch("copal_core.api.requests.get")
def test_get_manifest_sends_identity_headers(mock_get, _mock_hostname):
    mock_get.return_value = _mock_response(json_data={"files": []})

    api.get_manifest("proj", "v1.0")

    assert mock_get.called
    sent_headers = mock_get.call_args.kwargs["headers"]
    assert sent_headers["X-Copal-User"] == "stelios"
    assert sent_headers["X-Copal-Host"] == "LAPTOP"


@patch.dict(api.SETTINGS, {}, clear=True)
@patch("copal_core.api.socket.gethostname", return_value="")
def test_identity_headers_fall_back_to_unknown(_mock_hostname):
    headers = api._identity_headers()
    assert headers["X-Copal-User"] == "unknown"
    assert headers["X-Copal-Host"] == "unknown"


@patch("copal_core.api.requests.get")
def test_get_events_returns_empty_on_404(mock_get):
    mock_get.return_value = _mock_response(status_code=404)
    assert api.get_events("missing") == []


@patch("copal_core.api.requests.get")
def test_get_events_returns_parsed_list(mock_get):
    sample = [{"kind": "push", "version_tag": "v1.0", "user": "x", "host": "h", "created_at": "2026-05-13T12:00:00"}]
    mock_get.return_value = _mock_response(json_data=sample)
    assert api.get_events("proj") == sample
