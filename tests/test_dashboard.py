"""Tests for forge/dashboard.py."""

import json
import socket
import time
import urllib.request

from forge.dashboard import (
    push_event,
    update_dashboard_state,
    start_dashboard,
    stop_dashboard,
    INDEX_HTML,
    _dashboard_state,
    _sse_clients,
    _sse_lock,
)


def _reset_dashboard():
    """Reset global dashboard state between tests."""
    from forge import dashboard
    dashboard._dashboard_state.clear()
    with dashboard._sse_lock:
        dashboard._sse_clients.clear()
    dashboard._server = None
    dashboard._project_dir = None
    dashboard._stop_event.clear()


def test_push_event_no_clients():
    """push_event with no clients connected does not raise."""
    _reset_dashboard()
    push_event("test_event", {"key": "value"})


def test_push_event_with_disconnected_client():
    """Disconnected clients removed from registry."""
    _reset_dashboard()
    from forge import dashboard

    class FakeWfile:
        def write(self, data):
            raise BrokenPipeError("disconnected")
        def flush(self):
            pass

    with dashboard._sse_lock:
        dashboard._sse_clients.append(FakeWfile())
        assert len(dashboard._sse_clients) == 1

    push_event("test", {"data": "value"})

    with dashboard._sse_lock:
        assert len(dashboard._sse_clients) == 0


def test_update_dashboard_state_updates_dict():
    """update_dashboard_state merges into shared state dict."""
    _reset_dashboard()
    from forge import dashboard

    update_dashboard_state({"health": "A", "cost": "$0.12"})
    assert dashboard._dashboard_state["health"] == "A"
    assert dashboard._dashboard_state["cost"] == "$0.12"

    update_dashboard_state({"health": "B"})
    assert dashboard._dashboard_state["health"] == "B"
    assert dashboard._dashboard_state["cost"] == "$0.12"  # preserved


def _find_free_port():
    """Find a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_start_dashboard_returns_thread(tmp_path):
    """start_dashboard returns a running Thread."""
    _reset_dashboard()
    port = _find_free_port()
    thread = start_dashboard(tmp_path, port=port)
    try:
        assert thread is not None
        assert thread.is_alive()
    finally:
        stop_dashboard()
        time.sleep(0.2)


def test_start_dashboard_port_in_use(tmp_path, monkeypatch):
    """Returns None gracefully when port already bound."""
    _reset_dashboard()
    from http.server import HTTPServer

    # Force HTTPServer to raise OSError to simulate port-in-use
    original_init = HTTPServer.__init__

    def fake_init(self, *args, **kwargs):
        raise OSError("Address already in use")

    monkeypatch.setattr(HTTPServer, "__init__", fake_init)
    result = start_dashboard(tmp_path, port=3333)
    assert result is None


def test_dashboard_handler_get_state(tmp_path):
    """GET /state returns JSON response."""
    _reset_dashboard()
    port = _find_free_port()
    thread = start_dashboard(tmp_path, port=port)
    time.sleep(0.3)
    try:
        update_dashboard_state({"health": "A", "cost": "$0.50"})
        resp = urllib.request.urlopen(f"http://localhost:{port}/state", timeout=3)
        data = json.loads(resp.read())
        assert data["health"] == "A"
        assert data["cost"] == "$0.50"
    finally:
        stop_dashboard()
        time.sleep(0.2)


def test_dashboard_handler_get_log(tmp_path):
    """GET /log returns JSON array."""
    _reset_dashboard()
    port = _find_free_port()
    # Write some log entries
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    log_path = forge_dir / "build.log"
    log_path.write_text(
        json.dumps({"ts": "2024-01-01T00:00:00", "event": "test"}) + "\n"
    )
    thread = start_dashboard(tmp_path, port=port)
    time.sleep(0.3)
    try:
        resp = urllib.request.urlopen(f"http://localhost:{port}/log", timeout=3)
        data = json.loads(resp.read())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["event"] == "test"
    finally:
        stop_dashboard()
        time.sleep(0.2)


def test_dashboard_handler_404(tmp_path):
    """Unknown paths return 404."""
    _reset_dashboard()
    port = _find_free_port()
    thread = start_dashboard(tmp_path, port=port)
    time.sleep(0.3)
    try:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/nonexistent", timeout=3)
            assert False, "Should have raised"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        stop_dashboard()
        time.sleep(0.2)


def test_index_html_contains_required_elements():
    """INDEX_HTML contains SSE EventSource and key UI elements."""
    assert "EventSource" in INDEX_HTML
    assert "Forge" in INDEX_HTML
    assert "/events" in INDEX_HTML
    assert "/state" in INDEX_HTML
    assert "/log" in INDEX_HTML
    assert "phaseBar" in INDEX_HTML
    assert "costValue" in INDEX_HTML
    assert "healthValue" in INDEX_HTML
    assert "logContainer" in INDEX_HTML


def test_get_integration_statuses_all_missing(tmp_path):
    """Returns all dashes when no integrations configured."""
    from forge.commands.run import _get_integration_statuses
    statuses = _get_integration_statuses(tmp_path)
    assert statuses["github"] == "-"
    assert statuses["vercel"] == "-"
    assert statuses["linear"] == "-"
    assert statuses["sentry"] == "-"
    assert statuses["figma"] == "-"
    assert statuses["ollama"] == "-"


def test_get_integration_statuses_enabled(tmp_path):
    """Returns 'ok' for enabled integrations."""
    from forge.commands.run import _get_integration_statuses
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "github.json").write_text(json.dumps({"enabled": True}))
    (forge_dir / "ollama.json").write_text(json.dumps({"enabled": False}))
    statuses = _get_integration_statuses(tmp_path)
    assert statuses["github"] == "ok"
    assert statuses["ollama"] == "-"
    assert statuses["vercel"] == "-"
