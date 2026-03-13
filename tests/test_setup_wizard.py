"""Tests for forge/setup_wizard.py."""

import json
import socket
import time
import urllib.request

from forge.setup_wizard import (
    SETUP_HTML,
    format_requirements_md,
    handle_setup_submit,
    start_forge_run_subprocess,
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


def _find_free_port():
    """Find a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class FakeHandler:
    """Minimal mock of BaseHTTPRequestHandler for testing."""

    def __init__(self):
        self.response_code = None
        self.headers_sent = {}
        self.body_written = b""

    def send_response(self, code):
        self.response_code = code

    def send_header(self, key, value):
        self.headers_sent[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.body_written += data

    def flush(self):
        pass


# ---------------------------------------------------------------------------
# HTML content tests
# ---------------------------------------------------------------------------

def test_setup_html_contains_required_steps():
    """SETUP_HTML contains all 5 step indicators."""
    assert "Step 1" in SETUP_HTML
    assert "Step 2" in SETUP_HTML
    assert "Step 3" in SETUP_HTML
    assert "Step 4" in SETUP_HTML
    assert "Step 5" in SETUP_HTML
    assert len(SETUP_HTML) > 2000


def test_setup_html_contains_ai_assist_button():
    """SETUP_HTML has Draft with AI button."""
    assert "Draft with AI" in SETUP_HTML
    assert "/setup/ai-assist" in SETUP_HTML


# ---------------------------------------------------------------------------
# format_requirements_md tests
# ---------------------------------------------------------------------------

def test_format_requirements_md_all_sections():
    """Output contains all 4 section headers."""
    data = {
        "core_features": "User login\nUser signup",
        "pages_routes": "Home page\nDashboard",
        "data_models": "User (email, name)\nPost (title, body)",
        "non_functional": "Page load under 2s",
    }
    result = format_requirements_md(data)
    assert "## Core Features" in result
    assert "## Pages and Routes" in result
    assert "## Data Models" in result
    assert "## Non-Functional Requirements" in result
    assert "- [ ] User login" in result
    assert "- [ ] Page load under 2s" in result


def test_format_requirements_md_empty_fields():
    """Handles empty form fields without crashing."""
    result = format_requirements_md({})
    assert "## Core Features" in result
    assert "## Non-Functional Requirements" in result
    # Should still produce valid markdown structure
    assert result.startswith("# REQUIREMENTS.md")

    # Also test with empty strings
    result2 = format_requirements_md({
        "core_features": "",
        "pages_routes": "",
        "data_models": "",
        "non_functional": "",
    })
    assert "## Core Features" in result2


# ---------------------------------------------------------------------------
# handle_setup_submit tests
# ---------------------------------------------------------------------------

def test_handle_setup_submit_writes_vision(tmp_path, monkeypatch):
    """Writes VISION.md from form data."""
    monkeypatch.setattr(
        "forge.setup_wizard.start_forge_run_subprocess", lambda d: None
    )

    handler = FakeHandler()
    body = json.dumps({
        "name": "Test App",
        "description": "A test application",
        "vision": "# Test Vision\n\nThis is the vision.",
        "requirements": {"core_features": "Feature 1"},
        "integrations": {},
    }).encode()

    handle_setup_submit(handler, body, tmp_path)
    assert handler.response_code == 200

    vision_file = tmp_path / "VISION.md"
    assert vision_file.exists()
    assert "Test Vision" in vision_file.read_text(encoding="utf-8")


def test_handle_setup_submit_writes_requirements(tmp_path, monkeypatch):
    """Writes REQUIREMENTS.md from form data."""
    monkeypatch.setattr(
        "forge.setup_wizard.start_forge_run_subprocess", lambda d: None
    )

    handler = FakeHandler()
    body = json.dumps({
        "name": "Test App",
        "description": "A test",
        "vision": "Vision text",
        "requirements": {
            "core_features": "Login\nSignup",
            "pages_routes": "Home",
            "data_models": "",
            "non_functional": "Fast",
        },
        "integrations": {},
    }).encode()

    handle_setup_submit(handler, body, tmp_path)

    req_file = tmp_path / "REQUIREMENTS.md"
    assert req_file.exists()
    content = req_file.read_text(encoding="utf-8")
    assert "- [ ] Login" in content
    assert "- [ ] Signup" in content
    assert "- [ ] Fast" in content


def test_handle_setup_submit_writes_integration_config(tmp_path, monkeypatch):
    """Writes .forge/github.json when GitHub enabled."""
    monkeypatch.setattr(
        "forge.setup_wizard.start_forge_run_subprocess", lambda d: None
    )
    # Mock profile save to avoid writing to real home dir
    monkeypatch.setattr("forge.profile.save_profile", lambda p: tmp_path / "profile.yaml")
    monkeypatch.setattr("forge.profile.load_profile", lambda: {})

    handler = FakeHandler()
    body = json.dumps({
        "name": "Test App",
        "description": "A test",
        "vision": "Vision",
        "requirements": {},
        "integrations": {
            "github": {
                "enabled": True,
                "owner": "myorg",
                "repo": "myrepo",
                "token": "ghp_test123",
            }
        },
    }).encode()

    handle_setup_submit(handler, body, tmp_path)

    github_config = tmp_path / ".forge" / "github.json"
    assert github_config.exists()
    data = json.loads(github_config.read_text(encoding="utf-8"))
    assert data["enabled"] is True
    assert data["owner"] == "myorg"
    assert data["repo"] == "myrepo"
    assert data["create_prs"] is True  # default


# ---------------------------------------------------------------------------
# Subprocess tests
# ---------------------------------------------------------------------------

def test_start_forge_run_subprocess_never_raises(tmp_path):
    """start_forge_run_subprocess does not raise on missing forge."""
    # Point to a nonexistent directory - should not raise
    bad_path = tmp_path / "nonexistent" / "deeply" / "nested"
    start_forge_run_subprocess(bad_path)
    # If we get here without exception, the test passes


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------

def test_setup_route_registered(tmp_path):
    """GET /setup returns 200."""
    _reset_dashboard()
    from forge.dashboard import start_dashboard, stop_dashboard
    port = _find_free_port()

    thread = start_dashboard(tmp_path, port=port)
    assert thread is not None
    time.sleep(0.3)

    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/setup", timeout=3
        )
        html = resp.read().decode()
        assert resp.status == 200
        assert "Step 1" in html or "step-1" in html
    finally:
        stop_dashboard()
        time.sleep(0.2)


def test_root_redirects_to_setup_when_no_state(tmp_path):
    """GET / returns 302 to /setup when no state.json."""
    _reset_dashboard()
    from forge.dashboard import start_dashboard, stop_dashboard
    port = _find_free_port()

    # Ensure no state.json exists
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(exist_ok=True)
    state_file = forge_dir / "state.json"
    if state_file.exists():
        state_file.unlink()

    thread = start_dashboard(tmp_path, port=port)
    assert thread is not None
    time.sleep(0.3)

    try:
        # urllib follows redirects by default, so we check the final URL
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/", timeout=3
        )
        # After redirect, should end up at /setup content
        html = resp.read().decode()
        assert "Step 1" in html or "Setup" in html or "setup" in html
    finally:
        stop_dashboard()
        time.sleep(0.2)
